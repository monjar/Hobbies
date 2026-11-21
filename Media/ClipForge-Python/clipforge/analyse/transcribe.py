"""Speech transcription with word-level timestamps (faster-whisper, runs locally)."""

import subprocess
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import TranscribeCfg
from ..errors import MediaError, ProviderError
from ..types import Word


WHISPER_SAMPLE_RATE = 16000


def decode_for_whisper(path: Path) -> np.ndarray:
    """Mono 16 kHz float samples, decoded with ffmpeg.

    faster-whisper can decode files itself through PyAV, but its 1.2.1 release passes an
    argument that PyAV 19 removed, so the audio is decoded here instead.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(WHISPER_SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        message = proc.stderr.decode(errors="replace").strip()
        if "does not contain any stream" in message or "matches no streams" in message:
            return np.zeros(0, dtype=np.float32)  # no audio track: nothing to transcribe
        raise MediaError(f"could not decode audio from {path}: {message[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


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

        audio = decode_for_whisper(path)
        if audio.size == 0:
            return []
        try:
            if self._model is None:
                self._model = WhisperModel(self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type)
            segments, _info = self._model.transcribe(audio, word_timestamps=True)
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
