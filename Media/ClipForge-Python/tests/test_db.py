import pytest

from clipforge.db import DB, STATES, TRANSITIONS
from clipforge.errors import IllegalTransition

JOB = dict(angle="a", live=0, source_path="/x", source_sha256="s", template="t", caption_style="c")
HAPPY_PATH = ["ingested", "analysed", "scripted", "voiced", "rendered", "pending_review", "approved", "published"]


def test_happy_path_is_legal():
    db = DB(":memory:")
    job = db.create_job(**JOB)
    for state in HAPPY_PATH:
        db.transition(job, state)
    assert db.job(job)["state"] == "published"


def test_every_undeclared_transition_raises():
    for start_index in range(len(HAPPY_PATH) - 1):
        db = DB(":memory:")
        job = db.create_job(**JOB)
        for state in HAPPY_PATH[:start_index]:
            db.transition(job, state)
        current = db.job(job)["state"]
        for target in STATES:
            if target in TRANSITIONS[current] or target == "failed":
                continue
            with pytest.raises(IllegalTransition):
                db.transition(job, target)
        assert db.job(job)["state"] == current


def test_pending_review_cannot_skip_to_published():
    db = DB(":memory:")
    job = db.create_job(**JOB)
    for state in HAPPY_PATH[:6]:
        db.transition(job, state)
    with pytest.raises(IllegalTransition):
        db.transition(job, "published")


def test_failed_needs_reason_and_is_terminal():
    db = DB(":memory:")
    job = db.create_job(**JOB)
    with pytest.raises(IllegalTransition):
        db.transition(job, "failed")
    db.transition(job, "failed", "boom")
    assert db.job(job)["fail_reason"] == "boom"
    for target in ("ingested", "failed"):
        with pytest.raises(IllegalTransition):
            db.transition(job, target, "again")


def test_reopening_database_keeps_schema_and_data(tmp_path):
    path = tmp_path / "state" / "c.db"
    job = DB(path).create_job(**JOB)
    assert DB(path).job(job)["state"] == "discovered"
