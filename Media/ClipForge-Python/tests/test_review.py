import time

import pytest
from fastapi.testclient import TestClient

from clipforge.errors import ClipforgeError, IllegalTransition
from clipforge.review import actions
from clipforge.review.app import create_app


def seed(db, settings, state="pending_review", live=1) -> str:
    """A job in the given state with a (fake) render on disk, without running the pipeline."""
    job_id = db.create_job(
        angle="detail-spotting", live=live, source_path=str(settings.inbox / "clip.mp4"),
        source_sha256="sha", template="blur_fill", caption_style="karaoke",
    )
    source_id = db.insert(
        "sources", kind="local", path=str(settings.inbox / "clip.mp4"), policy_rule="local_inbox",
        sha256="sha", duration_s=32.0, ingested_at=time.time(),
    )
    db.set_job(job_id, source_id=source_id)
    db.insert(
        "moments", job_id=job_id, start_s=0, end_s=26, segments="[]", description="A red car at night.",
        transcript="", pick_reason="busiest stretch", spoiler_label="none", spoiler_reason="ordinary gameplay",
    )
    db.insert(
        "scripts", job_id=job_id, est_speech_s=24, attempts=1, created_at=time.time(),
        body='{"hook": "Watch the red car", "lines": [{"text": "It drifts.", "start": 2.0}], '
        '"title": "Red car", "description": "d", "hashtags": ["#gta6"]}',
    )
    settings.out.mkdir(exist_ok=True)
    video = settings.out / f"{job_id}.mp4"
    video.write_bytes(b"0123456789")
    sidecar = settings.out / f"{job_id}.json"
    sidecar.write_text('{"title": "Red car", "flags": []}')
    db.insert(
        "renders", job_id=job_id, path=str(video), sidecar_path=str(sidecar), duration_s=25.0,
        narration_coverage=0.9, max_source_segment_s=12.0, created_at=time.time(),
    )
    db.add_cost(job_id, "anthropic", "script", "claude-opus-5-5", 0.0412, {})
    for step in ("ingested", "analysed", "scripted", "voiced", "rendered", "pending_review"):
        db.transition(job_id, step)
        if step == state:
            break
    return job_id


@pytest.fixture
def client(settings, db):
    return TestClient(create_app(settings, background=False), follow_redirects=False)


def test_queue_lists_pending_jobs(client, db, settings):
    job_id = seed(db, settings)
    page = client.get("/")
    assert page.status_code == 200
    assert f"/job/{job_id}" in page.text and "Waiting for review (1)" in page.text
    assert 'name="viewport"' in page.text


def test_job_page_shows_player_script_source_verdict_and_cost(client, db, settings):
    job_id = seed(db, settings)
    page = client.get(f"/job/{job_id}").text
    assert f'src="/media/{job_id}"' in page
    assert "Watch the red car" in page and "It drifts." in page
    assert "local_inbox" in page and "clip.mp4" in page
    assert "ordinary gameplay" in page
    assert "$0.0412" in page
    for label in ("Approve", "Reject", "Regenerate script"):
        assert f">{label}</button>" in page


def test_media_supports_range_requests_for_phone_players(client, db, settings):
    job_id = seed(db, settings)
    part = client.get(f"/media/{job_id}", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"2345"
    assert part.headers["content-type"] == "video/mp4"


def test_approve_moves_job_to_approved(client, db, settings):
    job_id = seed(db, settings)
    assert client.post(f"/job/{job_id}/approve").status_code == 303
    assert db.job(job_id)["state"] == "approved"
    assert db.one("SELECT action FROM reviews WHERE job_id = ?", job_id)["action"] == "approve"


def test_reject_without_reason_is_refused(client, db, settings):
    job_id = seed(db, settings)
    for reason in ("", "   "):
        response = client.post(f"/job/{job_id}/reject", data={"reason": reason})
        assert response.status_code == 400 and "needs a reason" in response.text
    assert db.job(job_id)["state"] == "pending_review"
    assert db.one("SELECT COUNT(*) FROM reviews")[0] == 0


def test_reject_stores_reason(client, db, settings):
    job_id = seed(db, settings)
    assert client.post(f"/job/{job_id}/reject", data={"reason": "hook is clickbait"}).status_code == 303
    assert db.job(job_id)["state"] == "rejected"
    assert db.one("SELECT reason FROM reviews WHERE job_id = ?", job_id)["reason"] == "hook is clickbait"


def test_mock_render_cannot_be_approved(client, db, settings):
    job_id = seed(db, settings, live=0)
    response = client.post(f"/job/{job_id}/approve")
    assert response.status_code == 400 and "mock providers" in response.text
    assert db.job(job_id)["state"] == "pending_review"
    with pytest.raises(ClipforgeError):
        actions.approve(db, job_id)


@pytest.mark.parametrize("state", ["voiced", "rendered"])
def test_only_pending_jobs_can_be_decided(client, db, settings, state):
    job_id = seed(db, settings, state=state)
    assert client.post(f"/job/{job_id}/approve").status_code == 400
    assert client.post(f"/job/{job_id}/reject", data={"reason": "r"}).status_code == 400
    assert db.job(job_id)["state"] == state
    assert ">Approve</button>" not in client.get(f"/job/{job_id}").text


def test_decision_cannot_be_made_twice(client, db, settings):
    job_id = seed(db, settings)
    client.post(f"/job/{job_id}/approve")
    assert client.post(f"/job/{job_id}/reject", data={"reason": "changed my mind"}).status_code == 400
    with pytest.raises(IllegalTransition):
        actions.approve(db, job_id)
    assert db.job(job_id)["state"] == "approved"


def test_cross_site_form_posts_are_refused(client, db, settings):
    job_id = seed(db, settings)
    response = client.post(f"/job/{job_id}/approve", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert db.job(job_id)["state"] == "pending_review"
    assert client.post(f"/job/{job_id}/approve", headers={"Origin": "http://testserver"}).status_code == 303


def test_unknown_job_is_404(client):
    assert client.get("/job/nope").status_code == 404
    assert client.get("/media/nope").status_code == 404


class RecordingLLM:
    name = "fake"

    def __init__(self, inner):
        self.inner, self.script_prompts = inner, []

    def complete_json(self, *, task, system, user, schema):
        if task == "script":
            self.script_prompts.append(user)
        return self.inner.complete_json(task=task, system=system, user=user, schema=schema)


def test_regenerate_rejects_old_render_and_queues_a_new_one_with_feedback(settings, db, make_pipeline, inbox_clip):
    from clipforge.providers.mock import MockLLM

    llm = RecordingLLM(MockLLM(settings.style.speech_wps))
    pipeline = make_pipeline(llm=llm)
    old_id = pipeline.make(inbox_clip, "detail-spotting", live=False)
    client = TestClient(create_app(settings, pipeline_factory=lambda live: pipeline, background=False), follow_redirects=False)

    response = client.post(f"/job/{old_id}/regenerate", data={"feedback": "less hype"})
    assert response.status_code == 303
    new_id = response.headers["location"].removeprefix("/job/")

    assert db.job(old_id)["state"] == "rejected"
    new = db.job(new_id)
    assert new["state"] == "pending_review" and new["parent_job_id"] == old_id
    old_moment = db.one("SELECT start_s, end_s, description FROM moments WHERE job_id = ?", old_id)
    new_moment = db.one("SELECT start_s, end_s, description FROM moments WHERE job_id = ?", new_id)
    assert tuple(new_moment) == tuple(old_moment)  # same footage, no second analysis
    assert db.one("SELECT COUNT(*) FROM costs WHERE job_id = ? AND task = 'pick_window'", new_id)[0] == 0
    assert "- regenerate script: less hype" in llm.script_prompts[-1]
    assert db.one("SELECT path FROM renders WHERE job_id = ?", new_id)["path"] != db.one(
        "SELECT path FROM renders WHERE job_id = ?", old_id)["path"]
