"""One command, local file in, validated vertical MP4 out. Paid providers are mocked; ffmpeg is real."""

import json
from pathlib import Path

import pytest

from clipforge.errors import CostCapExceeded, SpoilerRejected, ValidationFailed
from clipforge.media import probe
from clipforge.providers.base import LLMResult
from clipforge.providers.mock import MockLLM
from clipforge.rules import MAX_SOURCE_SEGMENT_S, MIN_NARRATION_COVERAGE, NARRATION_LUFS


@pytest.fixture
def rendered(make_pipeline, db, inbox_clip):
    pipeline = make_pipeline()
    job_id = pipeline.make(inbox_clip, "detail-spotting", live=False)
    return pipeline, job_id


def test_make_produces_video_matching_output_spec(rendered, db):
    _, job_id = rendered
    assert db.job(job_id)["state"] == "pending_review"
    render = db.one("SELECT * FROM renders WHERE job_id = ?", job_id)
    info = probe(render["path"])
    assert (info.width, info.height) == (1080, 1920)
    assert info.fps == pytest.approx(30)
    assert (info.vcodec, info.acodec) == ("h264", "aac")
    assert "mp4" in info.format_name
    assert 20 <= info.duration <= 40
    assert render["narration_coverage"] >= MIN_NARRATION_COVERAGE
    assert render["max_source_segment_s"] <= MAX_SOURCE_SEGMENT_S


def test_sidecar_carries_metadata_attribution_cost_and_spoiler_verdict(rendered, db, inbox_clip):
    _, job_id = rendered
    render = db.one("SELECT * FROM renders WHERE job_id = ?", job_id)
    sidecar = json.loads(Path(render["sidecar_path"]).read_text())
    assert sidecar["title"] and sidecar["description"] and sidecar["hashtags"]
    assert sidecar["source_attribution"]["path"] == str(inbox_clip)
    assert sidecar["source_attribution"]["policy_rule"] == "local_inbox"
    assert sidecar["spoiler"]["label"] == "none"
    assert sidecar["cost"]["total_usd"] == 0 and sidecar["cost"]["cap_usd"] == 0.50
    assert sidecar["narration_lufs"] == pytest.approx(NARRATION_LUFS, abs=1.0)
    assert sidecar["live_providers"] is False and any("mock" in flag for flag in sidecar["flags"])
    assert len(sidecar["source_attribution"]["segments_s"]) >= 2


def test_every_provider_call_is_written_to_costs(rendered, db):
    _, job_id = rendered
    tasks = [row["task"] for row in db.all("SELECT task FROM costs WHERE job_id = ? ORDER BY id", job_id)]
    assert tasks[:3] == ["describe_frames", "pick_window", "spoiler"]
    assert {"script", "grounding", "narration"} <= set(tasks)


def test_rerunning_a_finished_job_is_a_no_op(rendered, db, inbox_clip):
    pipeline, job_id = rendered
    render_before = dict(db.one("SELECT * FROM renders WHERE job_id = ?", job_id))
    costs_before = db.one("SELECT COUNT(*) FROM costs")[0]
    assert pipeline.make(inbox_clip, "detail-spotting", live=False) == job_id
    pipeline.run(job_id)
    assert db.one("SELECT COUNT(*) FROM jobs")[0] == 1
    assert dict(db.one("SELECT * FROM renders WHERE job_id = ?", job_id)) == render_before
    assert db.one("SELECT COUNT(*) FROM costs")[0] == costs_before


def test_new_jobs_rotate_template_and_caption_style(rendered, make_pipeline, db, inbox_clip):
    first = db.job(rendered[1])
    second = db.job(make_pipeline().make(inbox_clip, "detail-spotting", live=False, new=True))
    assert second["id"] != first["id"]
    assert second["template"] != first["template"] and second["caption_style"] != first["caption_style"]
    assert second["state"] == "pending_review"


def test_source_without_audio_still_renders(make_pipeline, db, settings, fixture_clip):
    import subprocess

    silent = settings.inbox / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(fixture_clip), "-an", "-c:v", "copy", str(silent)], check=True)
    job_id = make_pipeline().make(silent, "detail-spotting", live=False)
    assert db.job(job_id)["state"] == "pending_review"
    assert probe(db.one("SELECT path FROM renders WHERE job_id = ?", job_id)["path"]).acodec == "aac"


class SpoilerLLM(MockLLM):
    def complete_json(self, *, task, system, user, schema):
        if task == "spoiler":
            return LLMResult(data={"label": "ending", "reason": "credits roll"}, usd=0.0, model="fake")
        return super().complete_json(task=task, system=system, user=user, schema=schema)


def test_spoiler_clip_is_rejected_before_any_script_or_render(make_pipeline, db, settings, inbox_clip):
    with pytest.raises(SpoilerRejected):
        make_pipeline(llm=SpoilerLLM()).make(inbox_clip, "detail-spotting", live=False)
    job = db.one("SELECT * FROM jobs")
    assert job["state"] == "failed" and "ending" in job["fail_reason"]
    assert db.one("SELECT spoiler_label FROM moments")["spoiler_label"] == "ending"
    assert db.one("SELECT COUNT(*) FROM scripts")[0] == 0
    assert not settings.out.exists()


class PriceyLLM(MockLLM):
    def complete_json(self, *, task, system, user, schema):
        result = super().complete_json(task=task, system=system, user=user, schema=schema)
        result.usd = 0.30
        return result


def test_job_aborts_when_cost_cap_is_exceeded(make_pipeline, db, settings, inbox_clip):
    with pytest.raises(CostCapExceeded):
        make_pipeline(llm=PriceyLLM()).make(inbox_clip, "detail-spotting", live=False)
    job = db.one("SELECT * FROM jobs")
    assert job["state"] == "failed" and "CostCapExceeded" in job["fail_reason"]
    assert db.total_cost(job["id"]) == pytest.approx(0.60)  # stopped at the call that crossed the cap
    assert not settings.out.exists()


class TerseLLM(MockLLM):
    """Writes far too few words to fill the clip."""

    def _script(self, user):
        data = super()._script(user)
        data["lines"] = data["lines"][:1]
        return data


def test_script_that_cannot_fill_the_clip_fails_the_job(make_pipeline, db, settings, inbox_clip):
    with pytest.raises(ValidationFailed):
        make_pipeline(llm=TerseLLM()).make(inbox_clip, "detail-spotting", live=False)
    assert db.one("SELECT state FROM jobs")["state"] == "failed"
    assert not settings.out.exists()


def test_failed_job_does_not_block_a_fresh_attempt(make_pipeline, db, inbox_clip):
    with pytest.raises(SpoilerRejected):
        make_pipeline(llm=SpoilerLLM()).make(inbox_clip, "detail-spotting", live=False)
    job_id = make_pipeline().make(inbox_clip, "detail-spotting", live=False)
    assert db.job(job_id)["state"] == "pending_review"
    assert db.one("SELECT COUNT(*) FROM jobs")[0] == 2
