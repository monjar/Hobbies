"""Metrics pull, the weekly report, and winners fed back into the script prompt."""

import json
import time
from datetime import date

import httpx
import pytest

from clipforge.errors import ClipforgeError, ProviderError
from clipforge.metrics.pull import add_manual, pull_youtube, record
from clipforge.metrics.report import build_report, hook_style, length_bucket, post_rows, top_scripts
from clipforge.review import actions
from helpers import seed

TODAY = date.today()


def posted(db, settings, platform="youtube", post_id="vidA", **seed_args) -> tuple[str, int]:
    job_id = seed(db, settings, state="approved", **seed_args)
    row = db.insert(
        "posts", job_id=job_id, platform=platform, status="published" if platform == "youtube" else "exported",
        post_id=post_id, url=f"https://example.test/{post_id}" if post_id else None, detail="{}", created_at=time.time(),
    )
    return job_id, row


def analytics(rows):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = [{"name": n} for n in ("video", "views", "likes", "shares", "averageViewDuration")]
        return httpx.Response(200, json={"columnHeaders": headers, "rows": rows})

    return seen, httpx.MockTransport(handler)


def test_pull_requests_per_video_totals_and_stores_a_snapshot(db, settings):
    _, row_a = posted(db, settings, post_id="vidA")
    _, row_b = posted(db, settings, post_id="vidB")
    posted(db, settings, platform="tiktok", post_id=None)
    seen, transport = analytics([["vidA", 1200, 80, 14, 17.5]])

    assert pull_youtube(db, lambda: "TOKEN", TODAY, transport) == 1
    params = dict(seen[0].url.params)
    assert seen[0].url.host == "youtubeanalytics.googleapis.com" and seen[0].url.path == "/v2/reports"
    assert params["ids"] == "channel==MINE" and params["dimensions"] == "video"
    assert params["metrics"] == "views,likes,shares,averageViewDuration"
    assert sorted(params["filters"].removeprefix("video==").split(",")) == ["vidA", "vidB"]  # no TikTok post
    assert params["endDate"] == TODAY.isoformat() and seen[0].headers["authorization"] == "Bearer TOKEN"

    stored = db.all("SELECT * FROM metrics")
    assert len(stored) == 1 and stored[0]["post_id"] == row_a
    assert (stored[0]["views"], stored[0]["likes"], stored[0]["shares"], stored[0]["avg_view_duration_s"]) == (1200, 80, 14, 17.5)


def test_pulling_twice_in_a_day_keeps_one_snapshot(db, settings):
    posted(db, settings)
    for views in (100, 150):
        pull_youtube(db, lambda: "T", TODAY, analytics([["vidA", views, 1, 0, 10.0]])[1])
    assert [r["views"] for r in db.all("SELECT views FROM metrics")] == [150]


def test_pull_without_youtube_posts_makes_no_request(db, settings):
    seen, transport = analytics([])
    assert pull_youtube(db, lambda: pytest.fail("no token needed"), TODAY, transport) == 0 and seen == []


def test_analytics_error_is_reported(db, settings):
    posted(db, settings)
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"error": {"message": "Forbidden"}}))
    with pytest.raises(ProviderError, match="Forbidden"):
        pull_youtube(db, lambda: "T", TODAY, transport)


def test_manual_metrics_need_an_existing_post(db, settings):
    job_id, row = posted(db, settings, platform="tiktok", post_id=None)
    add_manual(db, job_id, "tiktok", TODAY, views=900, likes=40, shares=3, avg_view_s=12.5)
    assert db.one("SELECT views, avg_view_duration_s FROM metrics WHERE post_id = ?", row)[:] == (900, 12.5)
    with pytest.raises(ClipforgeError, match="no instagram post"):
        add_manual(db, job_id, "instagram", TODAY, 1, 0, 0, None)


def test_hook_styles_and_length_buckets():
    assert hook_style("Did you see this?") == "question"
    assert hook_style("3 details in this alley") == "number"
    assert hook_style("You missed the reflection") == "second person"
    assert hook_style("Watch the red car") == "statement"
    assert (length_bucket(24.9), length_bucket(25.0), length_bucket(39.5)) == ("20-25s", "25-30s", "35-40s")


@pytest.fixture
def history(db, settings):
    """Four posts: retention 80%, 60%, 40% and one without view duration."""
    specs = [
        ("detail-spotting", 25.0, "Did you spot the cat?", 20.0, 5000),
        ("detail-spotting", 30.0, "Watch the red car", 18.0, 3000),
        ("mechanic-explained", 35.0, "3 rules of the wanted system", 14.0, 1000),
        ("mechanic-explained", 22.0, "You can swim here", None, 200),
    ]
    jobs = []
    for index, (angle, duration, hook, avg, views) in enumerate(specs):
        job_id, row = posted(db, settings, post_id=f"vid{index}", angle=angle, duration=duration, hook=hook)
        record(db, row, TODAY, views, views // 20, views // 100, avg)
        jobs.append(job_id)
    return jobs


def test_retention_is_average_view_time_over_clip_length(db, history):
    rows = {r["hook"]: r for r in post_rows(db)}
    assert rows["Did you spot the cat?"]["retention"] == pytest.approx(0.8)
    assert rows["3 rules of the wanted system"]["retention"] == pytest.approx(0.4)
    assert rows["You can swim here"]["retention"] is None


def test_report_ranks_and_groups_from_stored_data(db, settings, history):
    other = seed(db, settings)
    actions.reject(db, other, "hook oversells the clip")
    report = build_report(db, TODAY, days=7)

    assert "4 posts, 9200 views in total." in report
    best = report.split("## Best by retention")[1].split("##")[0]
    assert best.index("Did you spot the cat?") < best.index("Watch the red car") < best.index("3 rules")
    assert "You can swim here" not in best  # no view duration, so it cannot be ranked

    def table(title):
        block = report.split(f"### By {title}")[1].split("###")[0].split("## ")[0]
        return [line for line in block.splitlines() if line.startswith("| ") and "---" not in line][1:]

    assert table("angle") == ["| detail-spotting | 2 | 70% | 4000 |", "| mechanic-explained | 2 | 40% | 600 |"]
    assert "| blur_fill | 4 | 60% | 2300 |" in table("template")
    assert table("clip length")[0] == "| 25-30s | 1 | 80% | 5000 |"
    assert table("hook wording") == [
        "| question | 1 | 80% | 5000 |", "| statement | 1 | 60% | 3000 |", "| number | 1 | 40% | 1000 |",
        "| second person | 1 | n/a | 200 |",
    ]
    assert "- hook oversells the clip" in report.split("## Rejection reasons")[1]
    assert build_report(db, TODAY, days=7) == report


def test_report_without_data_says_so(db):
    assert "No posts with metrics" in build_report(db, TODAY)


def test_top_scripts_are_the_best_retained_ones(db, history):
    winners = top_scripts(db, count=2)
    assert len(winners) == 2
    assert winners[0].startswith("(80% retention, detail-spotting) Did you spot the cat? It drifts.")
    assert "Watch the red car" in winners[1]


def test_winning_scripts_reach_the_next_script_prompt(db, settings, history, make_pipeline, inbox_clip):
    from clipforge.providers.mock import MockLLM

    prompts = []

    class Spy(MockLLM):
        def complete_json(self, *, task, system, user, schema):
            if task == "script":
                prompts.append(user)
            return super().complete_json(task=task, system=system, user=user, schema=schema)

    make_pipeline(llm=Spy(settings.style.speech_wps)).make(inbox_clip, "detail-spotting", live=False)
    section = prompts[0].split("Scripts that performed well before:")[1].split("Reasons earlier scripts")[0]
    assert "(80% retention, detail-spotting) Did you spot the cat?" in section
    assert section.index("Did you spot the cat?") < section.index("Watch the red car")
