"""Unattended batch: works through inbox and allowed topics, stops at review, never publishes."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from clipforge.discover.rank import store_topics
from clipforge.discover.youtube import VideoMeta
from clipforge.errors import ClipforgeError, PolicyDenied
from clipforge.ingest.local import sha256_file
from clipforge.notify import send_webhook
from clipforge.scheduler import run_batch, worklist

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def topic(video_id, licence="creativeCommon", views=1000, duration=600, channel="UC_x", title="GTA 6 hidden details"):
    return VideoMeta(video_id, title, channel, "Chan", datetime(2026, 11, 10, tzinfo=UTC), duration, views, 0, licence)


def variant(src: Path, dest: Path, video_filter: str) -> Path:
    """A structurally different copy. (A recoloured copy would rightly count as duplicate footage.)"""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", video_filter, "-c:a", "copy",
                    "-preset", "ultrafast", str(dest)], check=True)
    return dest


def test_worklist_takes_inbox_first_then_allowed_topics_by_rank(db, settings, inbox_clip):
    (settings.inbox / "notes.txt").write_text("not a video")
    store_topics(db, [
        topic("cccccccccc1", views=5000), topic("cccccccccc2", views=9000),
        topic("ssssssssss1", licence="youtube", views=99999),          # standard licence: never downloaded
        topic("cccccccccc3", duration=15), topic("cccccccccc4", duration=4 * 3600),  # too short, too long
    ], "GTA 6", NOW)
    items = list(worklist(db, settings))
    assert items[0] == ("file", inbox_clip.resolve(), "detail-spotting")
    assert [target for kind, target, _ in items[1:]] == [
        "https://www.youtube.com/watch?v=cccccccccc2", "https://www.youtube.com/watch?v=cccccccccc1",
    ]
    assert items[1][2] == "detail-spotting"  # suggested from the title


def test_batch_makes_videos_up_to_the_limit_and_leaves_them_for_review(db, settings, make_pipeline, inbox_clip, fixture_clip):
    variant(fixture_clip, settings.inbox / "second.mp4", "negate")
    variant(fixture_clip, settings.inbox / "third.mp4", "transpose=1,scale=320:180")
    summary = run_batch(db, settings, make_pipeline(), jobs=2, live=False)
    assert len(summary["made"]) == 2 and summary["failed"] == [] and summary["pending_review"] == 2
    states = {row["state"] for row in db.all("SELECT state FROM jobs")}
    assert states == {"pending_review"}
    assert db.one("SELECT COUNT(*) FROM posts")[0] == 0

    again = run_batch(db, settings, make_pipeline(), jobs=5, live=False)  # picks up only what is left
    assert len(again["made"]) == 1 and db.one("SELECT COUNT(*) FROM jobs")[0] == 3


def test_failed_footage_is_not_retried_by_later_runs(db, settings, make_pipeline):
    short = settings.inbox / "short.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-t", "5", "-i", "testsrc2=size=160x90:rate=10", str(short)], check=True)
    first = run_batch(db, settings, make_pipeline(), jobs=3, live=False)
    assert first["made"] == [] and len(first["failed"]) == 1 and "at least" in first["failed"][0]["reason"]
    second = run_batch(db, settings, make_pipeline(), jobs=3, live=False)
    assert second == {"made": [], "failed": [], "pending_review": 0}
    assert db.one("SELECT COUNT(*) FROM jobs")[0] == 1


def test_topics_are_ingested_through_the_policy_and_one_failure_does_not_stop_the_run(db, settings, make_pipeline, fixture_clip):
    store_topics(db, [topic("cccccccccc1", views=9000), topic("cccccccccc2", views=5000)], "GTA 6", NOW)
    calls = []

    def ingest(url: str) -> Path:
        calls.append(url)
        if url.endswith("cccccccccc1"):
            raise PolicyDenied("licence changed to 'youtube'")
        dest = settings.library / "cccccccccc2.mp4"
        dest.parent.mkdir(exist_ok=True)
        shutil.copy(fixture_clip, dest)
        db.insert("sources", kind="url", path=str(dest), url=url, channel="Chan (UC_x)", licence="creativeCommon",
                  policy_rule="creative_commons", sha256=sha256_file(dest),
                  duration_s=32.0, ingested_at=1.0)
        return dest

    summary = run_batch(db, settings, make_pipeline(), jobs=2, live=False, ingest=ingest)
    assert len(summary["made"]) == 1 and summary["failed"][0]["reason"] == "licence changed to 'youtube'"
    assert db.job(summary["made"][0])["state"] == "pending_review"

    calls.clear()
    run_batch(db, settings, make_pipeline(), jobs=2, live=False, ingest=ingest)
    assert calls == []  # the refused URL and the finished one are both remembered


def test_without_an_ingest_function_topics_are_skipped(db, settings, make_pipeline):
    store_topics(db, [topic("cccccccccc1")], "GTA 6", NOW)
    assert run_batch(db, settings, make_pipeline(), jobs=2, live=False)["made"] == []


def test_discovery_failure_does_not_stop_local_work(db, settings, make_pipeline, inbox_clip):
    def broken():
        raise ClipforgeError("quotaExceeded")

    assert len(run_batch(db, settings, make_pipeline(), jobs=1, live=False, discover=broken)["made"]) == 1


def test_notification_is_sent_only_when_something_waits(db, settings, make_pipeline, inbox_clip):
    messages = []
    run_batch(db, settings, make_pipeline(), jobs=0, live=False, notify=messages.append)
    assert messages == []
    run_batch(db, settings, make_pipeline(), jobs=1, live=False, notify=messages.append)
    assert messages == ["ClipForge: 1 video waiting for review"]


def test_webhook_posts_json_text_and_reports_failures():
    seen = []

    def ok(request):
        seen.append(request)
        return httpx.Response(200)

    send_webhook("https://hooks.example.test/x", "hello", httpx.MockTransport(ok))
    assert seen[0].method == "POST" and seen[0].content == b'{"text":"hello"}'
    with pytest.raises(ClipforgeError, match="notification failed"):
        send_webhook("https://hooks.example.test/x", "hello", httpx.MockTransport(lambda r: httpx.Response(500)))
