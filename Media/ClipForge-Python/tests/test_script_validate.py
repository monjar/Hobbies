import pytest

from clipforge.costs import CostTracker
from clipforge.errors import ValidationFailed
from clipforge.providers.base import LLMResult
from clipforge.script.validate import estimate_speech_s, validate_script
from clipforge.script.writer import MAX_ATTEMPTS, write_script

WPS = 2.5
RENDER_S = 24.0
GROUNDING = "Footage description: A red car drives past 3 palm trees at night.\nSpeech in the footage: (no speech)"


def script(hook="Look closely at this red car", words=54, **overrides):
    body = " ".join(["word"] * words)
    data = {
        "hook": hook,
        "lines": [{"text": body, "start": 2.5}],
        "title": "Red car at night",
        "description": "A detail you missed.",
        "hashtags": ["#gta6", "#gaming"],
    }
    data.update(overrides)
    return data


def test_valid_script_passes():
    assert validate_script(script(), RENDER_S, WPS, GROUNDING) == []


def test_hook_over_twelve_words_is_rejected():
    hook = "this hook has far too many words in it to ever stop anyone scrolling"
    errors = validate_script(script(hook=hook, words=47), RENDER_S, WPS, GROUNDING)
    assert any("hook is 14 words" in e for e in errors)


def test_twelve_word_hook_is_allowed():
    hook = "one two three four five six seven eight nine ten eleven twelve"
    assert validate_script(script(hook=hook, words=48), RENDER_S, WPS, GROUNDING) == []


def test_hook_must_be_one_sentence():
    errors = validate_script(script(hook="Look at this. Then look again."), RENDER_S, WPS, GROUNDING)
    assert any("single sentence" in e for e in errors)


@pytest.mark.parametrize("words, ok", [(48, True), (47, False), (60, True), (61, False)])
def test_speech_time_must_be_within_ten_percent_of_clip(words, ok):
    # 6-word hook + body at 2.5 words/s against 24 s: allowed range is 54-66 words in total.
    draft = script(words=words)
    assert round(estimate_speech_s(draft, WPS) * WPS) == words + 6
    errors = validate_script(draft, RENDER_S, WPS, GROUNDING)
    assert (not any("narration is" in e for e in errors)) is ok


def test_numbers_missing_from_inputs_are_rejected():
    draft = script(lines=[{"text": "It launches in 2026 with " + " ".join(["word"] * 49), "start": 2.5}])
    errors = validate_script(draft, RENDER_S, WPS, GROUNDING)
    assert any("2026" in e for e in errors)


def test_numbers_present_in_inputs_are_accepted():
    draft = script(lines=[{"text": "Count the 3 palm trees " + " ".join(["word"] * 49), "start": 2.5}])
    assert validate_script(draft, RENDER_S, WPS, GROUNDING) == []


def test_line_times_must_be_ordered_and_inside_clip():
    backwards = script(lines=[{"text": " ".join(["word"] * 27), "start": 9.0}, {"text": " ".join(["word"] * 27), "start": 4.0}])
    assert any("backwards" in e for e in validate_script(backwards, RENDER_S, WPS, GROUNDING))
    late = script(lines=[{"text": " ".join(["word"] * 54), "start": 30.0}])
    assert any("inside the clip" in e for e in validate_script(late, RENDER_S, WPS, GROUNDING))


def test_malformed_hashtags_are_rejected():
    assert any("hashtag" in e for e in validate_script(script(hashtags=["gta6"]), RENDER_S, WPS, GROUNDING))


class ScriptedLLM:
    """Returns queued script drafts and grounding verdicts, recording the prompts it saw."""

    name = "fake"

    def __init__(self, drafts, claims=None):
        self.drafts, self.claims, self.prompts = list(drafts), list(claims or []), []

    def complete_json(self, *, task, system, user, schema):
        if task == "script":
            self.prompts.append(user)
            return LLMResult(data=self.drafts.pop(0), usd=0.01, model="fake")
        return LLMResult(data={"ungrounded_claims": self.claims.pop(0) if self.claims else []}, usd=0.01, model="fake")


@pytest.fixture
def costs(db):
    job = db.create_job(angle="a", live=0, source_path="/x", source_sha256="s", template="t", caption_style="c")
    return CostTracker(db, job, 5.0)


def write(llm, costs, **extra):
    return write_script(
        job_id=costs.job_id, angle="detail-spotting", angle_brief="Spot details.",
        description="A red car drives past 3 palm trees at night.", transcript="",
        render_s=RENDER_S, wps=WPS, llm=llm, costs=costs, **extra,
    )


def test_writer_retries_with_validator_feedback(costs):
    llm = ScriptedLLM([script(words=10), script()])
    result, attempts = write(llm, costs)
    assert attempts == 2 and result == script()
    assert "rejected by the validator" not in llm.prompts[0]
    assert "narration is 16 words" in llm.prompts[1]


def test_writer_rejects_ungrounded_claims(costs):
    llm = ScriptedLLM([script(), script()], claims=[["The car is a Banshee"], []])
    _, attempts = write(llm, costs)
    assert attempts == 2
    assert "unsupported claim: The car is a Banshee" in llm.prompts[1]


def test_writer_gives_up_after_max_attempts(costs):
    llm = ScriptedLLM([script(words=10)] * MAX_ATTEMPTS)
    with pytest.raises(ValidationFailed):
        write(llm, costs)
    assert len(llm.prompts) == MAX_ATTEMPTS


def test_prompt_carries_clip_length_angle_and_past_rejections(costs):
    llm = ScriptedLLM([script()])
    write(llm, costs, rejections=["too much hype"])
    prompt = llm.prompts[0]
    assert "Clip length: 24.0 seconds" in prompt
    assert "detail-spotting" in prompt and "- too much hype" in prompt


def test_script_calls_are_costed(costs):
    write(ScriptedLLM([script()]), costs)
    assert costs.total == pytest.approx(0.02)  # one script call and one grounding call
