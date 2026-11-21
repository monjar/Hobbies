"""Loads config/*.yaml and .env, validates, and exposes one Settings object."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import ConfigError


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoveryCfg(_Cfg):
    keywords: list[str] = []
    max_results: int = Field(default=25, ge=1, le=50)
    lookback_days: int = Field(default=7, ge=1)
    creative_commons_search: bool = True  # second search per keyword, limited to CC uploads


class SourcesCfg(_Cfg):
    inbox: str = "inbox"
    library: str = "library"
    allowlist_channels: list[str] = []
    allow_creative_commons: bool = True
    discovery: DiscoveryCfg = DiscoveryCfg()


class ModelCfg(_Cfg):
    provider: Literal["anthropic"]
    model: str
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    price_in_per_mtok: float = Field(ge=0)
    price_out_per_mtok: float = Field(ge=0)


class TTSCfg(_Cfg):
    provider: Literal["elevenlabs"]
    model: str
    voice_id: str
    output_format: str = "mp3_44100_128"
    price_per_1k_chars: float = Field(ge=0)


class TranscribeCfg(_Cfg):
    provider: Literal["faster-whisper", "none"] = "faster-whisper"
    model: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"


class ProvidersCfg(_Cfg):
    llm: ModelCfg
    vision: ModelCfg
    tts: TTSCfg
    transcribe: TranscribeCfg = TranscribeCfg()


class YouTubePublishCfg(_Cfg):
    mode: Literal["api", "export"] = "api"
    privacy: Literal["public", "unlisted", "private"] = "public"
    category_id: str = "20"
    contains_synthetic_media: bool = False


class TikTokPublishCfg(_Cfg):
    mode: Literal["export"] = "export"
    ai_disclosure: bool = True


class InstagramPublishCfg(_Cfg):
    mode: Literal["api", "export"] = "export"
    ai_disclosure: bool = True
    api_version: str = "v27.0"


class PublishCfg(_Cfg):
    youtube: YouTubePublishCfg = YouTubePublishCfg()
    tiktok: TikTokPublishCfg = TikTokPublishCfg()
    instagram: InstagramPublishCfg = InstagramPublishCfg()


class CaptionStyleCfg(_Cfg):
    name: str
    mode: Literal["karaoke", "word"]
    font: str
    size: int = Field(gt=0)
    primary: str = "FFFFFF"  # RRGGBB
    highlight: str = "FFD400"
    outline_px: int = 6


class OutputCfg(_Cfg):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    min_s: float = 20
    max_s: float = 40
    preset: str = "medium"
    crf: int = 20


class StyleCfg(_Cfg):
    angles: dict[str, str]
    templates: list[str]
    caption_styles: list[CaptionStyleCfg]
    output: OutputCfg = OutputCfg()
    speech_wps: float = Field(default=2.6, gt=0)
    game_gain_db: float = -10

    @model_validator(mode="after")
    def _variety(self):
        from .render.templates import TEMPLATES

        unknown = [t for t in self.templates if t not in TEMPLATES]
        if unknown:
            raise ValueError(f"unknown templates {unknown}; available: {sorted(TEMPLATES)}")
        if len(set(self.templates)) < 3:
            raise ValueError("at least 3 visual templates are required (PLAN.md section 8)")
        if len({c.name for c in self.caption_styles}) < 2:
            raise ValueError("at least 2 caption styles are required (PLAN.md section 8)")
        if not self.angles:
            raise ValueError("at least one narration angle is required")
        return self


@dataclass(frozen=True)
class Settings:
    root: Path
    sources: SourcesCfg
    providers: ProvidersCfg
    style: StyleCfg
    publish: PublishCfg
    env: dict[str, str]
    max_cost_usd: float

    @property
    def inbox(self) -> Path:
        return (self.root / self.sources.inbox).resolve()

    @property
    def library(self) -> Path:
        return (self.root / self.sources.library).resolve()

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def db_path(self) -> Path:
        return self.root / "state" / "clipforge.db"

    def secret(self, name: str) -> str:
        value = self.env.get(name, "")
        if not value:
            raise ConfigError(f"{name} is not set; add it to .env (see .env.example)")
        return value


def _load_yaml(path: Path, model):
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        return model.model_validate(yaml.safe_load(path.read_text()) or {})
    except (ValidationError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_settings(root: Path | str | None = None) -> Settings:
    root = Path(root or os.environ.get("CLIPFORGE_HOME") or Path.cwd()).resolve()
    env = {k: v for k, v in dotenv_values(root / ".env").items() if v}
    env.update({k: v for k, v in os.environ.items() if v})
    try:
        max_cost = float(env.get("MAX_COST_PER_VIDEO_USD", "0.50"))
    except ValueError as exc:
        raise ConfigError("MAX_COST_PER_VIDEO_USD must be a number") from exc
    if max_cost <= 0:
        raise ConfigError("MAX_COST_PER_VIDEO_USD must be positive")
    return Settings(
        root=root,
        sources=_load_yaml(root / "config" / "sources.yaml", SourcesCfg),
        providers=_load_yaml(root / "config" / "providers.yaml", ProvidersCfg),
        style=_load_yaml(root / "config" / "style.yaml", StyleCfg),
        publish=_load_yaml(root / "config" / "publish.yaml", PublishCfg),
        env=env,
        max_cost_usd=max_cost,
    )
