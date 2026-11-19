"""Speech transcription with word-level timestamps (faster-whisper, runs locally)."""

from pathlib import Path
from typing import Protocol

from ..config import TranscribeCfg
from ..errors import ProviderError
from ..types import Word


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> list[Word]: ...


class NullTranscriber:
    """Explicit choice (transcribe.provider: none) for footage without commentary."""

    def transcribe(self, path: Path) -> list[Word]:
        return []


class FasterWhisperTranscriber:
    def __init__(self, cfg: TranscribeCfg):
        self.cfg = cfg
        self._model = None

    def transcribe(self, path: Path) -> list[Word]:
        from faster_whisper import WhisperModel

        try:
            if self._model is None:
                self._model = WhisperModel(self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type)
            segments, _info = self._model.transcribe(str(path), word_timestamps=True)
            return [
                Word(word.word.strip(), float(word.start), float(word.end))
                for segment in segments
                for word in (segment.words or [])
                if word.word.strip()
            ]
        except Exception as exc:  # model download, decode and runtime errors all surface here
            raise ProviderError(
                f"transcription failed ({type(exc).__name__}: {exc}). The whisper model is downloaded on first "
                "use; set transcribe.provider to 'none' in config/providers.yaml to run without a transcript."
            ) from exc


def build_transcriber(cfg: TranscribeCfg) -> Transcriber:
    return NullTranscriber() if cfg.provider == "none" else FasterWhisperTranscriber(cfg)


def excerpt(words: list[Word], start: float, end: float) -> str:
    return " ".join(word.text for word in words if start <= word.start < end)
