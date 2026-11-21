"""Shared test helpers."""

import json
import time


def seed(db, settings, state="pending_review", live=1, angle="detail-spotting", duration=25.0, hook="Watch the red car") -> str:
    """A job in the given state with a (fake) render on disk, without running the pipeline."""
    job_id = db.create_job(
        angle=angle, live=live, source_path=str(settings.inbox / "clip.mp4"),
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
        body=json.dumps({"hook": hook, "lines": [{"text": "It drifts.", "start": 2.0}],
                         "title": "Red car", "description": "d", "hashtags": ["#gta6"]}),
    )
    settings.out.mkdir(exist_ok=True)
    video = settings.out / f"{job_id}.mp4"
    video.write_bytes(b"0123456789")
    sidecar = settings.out / f"{job_id}.json"
    sidecar.write_text(json.dumps({
        "title": "Red car", "description": "A detail you missed.", "hashtags": ["#gta6", "#gaming"],
        "template": "blur_fill", "flags": [],
    }))
    db.insert(
        "renders", job_id=job_id, path=str(video), sidecar_path=str(sidecar), duration_s=duration,
        narration_coverage=0.9, max_source_segment_s=12.0, created_at=time.time(),
    )
    db.add_cost(job_id, "anthropic", "script", "claude-opus-5-5", 0.0412, {})
    for step in ("ingested", "analysed", "scripted", "voiced", "rendered", "pending_review", "approved"):
        db.transition(job_id, step)
        if step == state:
            break
    return job_id
