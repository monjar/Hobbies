"""Request shape, cost maths and error handling of the real providers, with the network stubbed."""

import base64
import json
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from clipforge.config import ModelCfg, TTSCfg
from clipforge.errors import ProviderError
from clipforge.providers.anthropic_provider import AnthropicProvider
from clipforge.voice.providers.elevenlabs import ElevenLabsTTS

MODEL = ModelCfg(provider="anthropic", model="claude-opus-5-5", effort="medium", price_in_per_mtok=4.0, price_out_per_mtok=20.0)
SCHEMA = {"type": "object", "properties": {"choice": {"type": "integer"}}, "required": ["choice"], "additionalProperties": False}


def response(text='{"choice": 1}', stop_reason="end_turn", model="claude-opus-5-5", stop_details=None):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason, stop_details=stop_details, model=model,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
    )


@pytest.fixture
def provider():
    provider = AnthropicProvider("test-key", MODEL)
    provider.calls = []

    def create(**kwargs):
        provider.calls.append(kwargs)
        return provider.reply

    provider.reply = response()
    provider.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return provider


def test_json_call_sends_schema_effort_and_fallbacks(provider):
    result = provider.complete_json(task="pick_window", system="sys", user="hello", schema=SCHEMA)
    (call,) = provider.calls
    assert call["model"] == "claude-opus-5-5" and call["system"] == "sys"
    assert call["messages"] == [{"role": "user", "content": "hello"}]
    assert call["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in call and "temperature" not in call
    assert result.data == {"choice": 1}


def test_cost_uses_configured_token_prices(provider):
    result = provider.complete_json(task="t", system="s", user="u", schema=SCHEMA)
    assert result.usd == pytest.approx((1000 * 4.0 + 500 * 20.0) / 1_000_000)
    assert result.detail == {"input_tokens": 1000, "output_tokens": 500}


def test_cost_is_flagged_when_a_fallback_model_served_the_request(provider):
    provider.reply = response(model="claude-opus-4-8")
    result = provider.complete_json(task="t", system="s", user="u", schema=SCHEMA)
    assert result.model == "claude-opus-4-8" and "cost_is_estimate" in result.detail


def test_refusal_and_truncation_raise_instead_of_returning_content(provider):
    provider.reply = response(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(ProviderError, match="declined"):
        provider.complete_json(task="t", system="s", user="u", schema=SCHEMA)
    provider.reply = response(stop_reason="max_tokens")
    with pytest.raises(ProviderError, match="max_tokens"):
        provider.complete_json(task="t", system="s", user="u", schema=SCHEMA)


def test_vision_call_sends_frames_as_base64_jpeg_before_the_prompt(provider, tmp_path):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"\xff\xd8jpeg")
    provider.reply = response(text=" A car. ")
    result = provider.describe(frames=[frame, frame], prompt="Describe")
    content = provider.calls[0]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["image", "image", "text"]
    assert content[0]["source"] == {
        "type": "base64", "media_type": "image/jpeg", "data": base64.standard_b64encode(b"\xff\xd8jpeg").decode(),
    }
    assert "format" not in provider.calls[0]["output_config"] and "system" not in provider.calls[0]
    assert result.text == "A car."


TTS = TTSCfg(provider="elevenlabs", model="eleven_multilingual_v2", voice_id="VOICE", price_per_1k_chars=0.08)


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("tts") / "a.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", str(path)], check=True)
    return path.read_bytes()


def test_elevenlabs_request_and_result(tmp_path, mp3_bytes):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), key=request.headers["xi-api-key"], body=json.loads(request.content))
        text = "Hi you"
        return httpx.Response(200, json={
            "audio_base64": base64.b64encode(mp3_bytes).decode(),
            "alignment": {
                "characters": list(text),
                "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
                "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(len(text))],
            },
        })

    tts = ElevenLabsTTS("secret", TTS, transport=httpx.MockTransport(handler))
    result = tts.synthesize("Hi you", tmp_path / "line_00", previous_text="Before.")
    assert seen["url"] == "https://api.elevenlabs.io/v1/text-to-speech/VOICE/with-timestamps?output_format=mp3_44100_128"
    assert seen["key"] == "secret"
    assert seen["body"] == {"text": "Hi you", "model_id": "eleven_multilingual_v2", "previous_text": "Before."}
    assert result.path == tmp_path / "line_00.mp3" and result.path.read_bytes() == mp3_bytes
    assert result.duration == pytest.approx(1.0, abs=0.1)
    assert [w.text for w in result.words] == ["Hi", "you"]
    assert result.usd == pytest.approx(6 / 1000 * 0.08) == tts.estimate_usd("Hi you")


def test_elevenlabs_http_error_raises_and_writes_nothing(tmp_path):
    tts = ElevenLabsTTS("bad", TTS, transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "no"})))
    with pytest.raises(ProviderError, match="401"):
        tts.synthesize("Hi", tmp_path / "line_00")
    assert list(tmp_path.iterdir()) == []
