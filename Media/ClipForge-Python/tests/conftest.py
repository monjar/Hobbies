import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from clipforge.config import load_settings
from clipforge.db import DB
from clipforge.pipeline import Pipeline, Providers
from clipforge.providers.mock import MockLLM, MockTTS, MockVision
from clipforge.types import Word

REPO = Path(__file__).resolve().parent.parent
SHOTS = [("testsrc2", 7), ("smptebars", 6), ("mandelbrot", 8), ("rgbtestsrc", 5), ("yuvtestsrc", 6)]


@pytest.fixture(scope="session")
def fixture_clip(tmp_path_factory) -> Path:
    """A 32 s clip with five visibly different shots and a tone, built once per test run."""
    path = tmp_path_factory.mktemp("fixture") / "clip.mp4"
    inputs = []
    for name, seconds in SHOTS:
        inputs += ["-f", "lavfi", "-t", str(seconds), "-i", f"{name}=size=320x180:rate=30"]
    inputs += ["-f", "lavfi", "-t", "32", "-i", "sine=frequency=440:sample_rate=48000"]
    labels = "".join(f"[{i}:v]" for i in range(len(SHOTS)))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs,
         "-filter_complex", f"{labels}concat=n={len(SHOTS)}:v=1:a=0,format=yuv420p[v]",
         "-map", "[v]", "-map", f"{len(SHOTS)}:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """An isolated project root with the repo's config, tuned for fast encodes."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    style_path = tmp_path / "config" / "style.yaml"
    style = yaml.safe_load(style_path.read_text())
    style["output"]["preset"] = "ultrafast"
    style_path.write_text(yaml.safe_dump(style))
    (tmp_path / "inbox").mkdir()
    monkeypatch.setenv("MAX_COST_PER_VIDEO_USD", "0.50")
    return tmp_path


@pytest.fixture
def settings(root):
    return load_settings(root)


@pytest.fixture
def db(settings) -> DB:
    return DB(settings.db_path)


@pytest.fixture
def inbox_clip(settings, fixture_clip) -> Path:
    dest = settings.inbox / "clip.mp4"
    shutil.copy(fixture_clip, dest)
    return dest


class FakeTranscriber:
    def transcribe(self, path):
        return [Word(f"word{i}", 1.0 + i * 0.5, 1.4 + i * 0.5) for i in range(40)]


@pytest.fixture
def make_pipeline(settings, db):
    def build(llm=None, vision=None, tts=None) -> Pipeline:
        wps = settings.style.speech_wps
        providers = Providers(llm or MockLLM(wps), vision or MockVision(), tts or MockTTS(wps), FakeTranscriber())
        return Pipeline(settings, db, providers)

    return build
