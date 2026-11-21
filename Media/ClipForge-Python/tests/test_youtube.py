"""YouTube discovery and gated URL ingest, with the network and the downloader stubbed."""

import dataclasses
import json
import shutil
from datetime import UTC, datetime

import httpx
import pytest

from clipforge.config import load_settings
from clipforge.discover.rank import discover, ranked_topics, store_topics, suggest_angle, topic_context, views_per_hour
from clipforge.discover.youtube import VideoMeta, YouTubeClient, parse_duration, video_id_from_url
from clipforge.errors import PolicyDenied, ProviderError
from clipforge.ingest.policy import RULE_ALLOWLISTED_CHANNEL, RULE_CREATIVE_COMMONS, check_url
from clipforge.ingest.url import ingest_url

CC = VideoMeta("aaaaaaaaaaa", "GTA 6 hidden details", "UC_cc", "CC Channel", datetime(2026, 11, 1, tzinfo=UTC), 600, 240_000, 9000, "creativeCommon")
STD = dataclasses.replace(CC, id="bbbbbbbbbbb", title="GTA 6 vs GTA V", channel_id="UC_std", licence="youtube", views=960_000)
NOW = datetime(2026, 11, 11, tzinfo=UTC)  # 240 hours after publication


def api_item(video: VideoMeta) -> dict:
    return {
        "id": video.id,
        "snippet": {"title": video.title, "channelId": video.channel_id, "channelTitle": video.channel_title,
                    "publishedAt": video.published_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
        "contentDetails": {"duration": "PT10M"},
        "statistics": {"viewCount": str(video.views), "likeCount": str(video.likes)},
        "status": {"license": video.licence},
    }


class FakeApi:
    def __init__(self, videos=(CC, STD)):
        self.videos, self.requests = {v.id: v for v in videos}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = dict(request.url.params)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"items": [{"id": {"videoId": i}} for i in self.videos]})
        ids = params["id"].split(",")
        return httpx.Response(200, json={"items": [api_item(self.videos[i]) for i in ids if i in self.videos]})


def client(api, on_call=None):
    return YouTubeClient("KEY", on_call=on_call, transport=httpx.MockTransport(api))


@pytest.mark.parametrize("value, seconds", [("PT10M", 600), ("PT1H2M3S", 3723), ("PT45S", 45), ("P1DT1S", 86401), ("", 0), ("P0D", 0)])
def test_duration_parsing(value, seconds):
    assert parse_duration(value) == seconds


@pytest.mark.parametrize("url, video_id", [
    ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "aaaaaaaaaaa"),
    ("https://youtube.com/watch?v=aaaaaaaaaaa&t=30s", "aaaaaaaaaaa"),
    ("https://youtu.be/aaaaaaaaaaa", "aaaaaaaaaaa"),
    ("https://m.youtube.com/shorts/aaaaaaaaaaa", "aaaaaaaaaaa"),
    ("https://www.youtube.com/playlist?list=PL123", None),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://youtube.com.evil.example/watch?v=aaaaaaaaaaa", None),
    ("https://vimeo.com/12345", None),
    ("file:///etc/passwd", None),
    ("not a url", None),
])
def test_video_id_extraction(url, video_id):
    assert video_id_from_url(url) == video_id


def test_search_request_shape():
    api = FakeApi()
    ids = client(api).search("GTA 6", published_after=datetime(2026, 11, 4, tzinfo=UTC), licence="creativeCommon", max_results=10)
    params = dict(api.requests[0].url.params)
    assert api.requests[0].url.path == "/youtube/v3/search"
    assert params == {
        "part": "snippet", "type": "video", "q": "GTA 6", "order": "viewCount", "videoLicense": "creativeCommon",
        "maxResults": "10", "publishedAfter": "2026-11-04T00:00:00Z", "key": "KEY",
    }
    assert ids == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_videos_are_parsed_and_batched_fifty_per_call():
    api = FakeApi()
    found = client(api).videos(["aaaaaaaaaaa", "bbbbbbbbbbb"] + [f"x{i:010d}" for i in range(60)])
    assert found == [CC, STD]
    assert len(api.requests) == 2 and len(api.requests[0].url.params["id"].split(",")) == 50
    assert api.requests[0].url.params["part"] == "snippet,contentDetails,statistics,status"


def test_api_errors_never_leak_the_key():
    def denied(request):
        return httpx.Response(403, json={"error": {"message": "quotaExceeded"}})

    with pytest.raises(ProviderError) as info:
        client(denied).search("GTA 6")
    assert "quotaExceeded" in str(info.value) and "KEY" not in str(info.value)
    assert info.value.__cause__ is None


def test_views_per_hour_and_floor_for_new_uploads():
    assert views_per_hour(240_000, CC.published_at, NOW) == 1000
    assert views_per_hour(500, NOW, NOW) == 500


def test_ranking_is_deterministic_from_stored_data(db):
    twin = dataclasses.replace(CC, id="ccccccccccc")
    store_topics(db, [twin, CC, STD], "GTA 6", NOW)
    order = [t["video_id"] for t in ranked_topics(db)]
    assert order == ["bbbbbbbbbbb", "aaaaaaaaaaa", "ccccccccccc"]  # by views/hour, ties by ID
    assert ranked_topics(db) == ranked_topics(db)
    assert "GTA 6 vs GTA V (4000 views/hour)" in topic_context(db).splitlines()[0]


def test_refetching_a_topic_updates_it_instead_of_duplicating(db):
    store_topics(db, [CC], "GTA 6", NOW)
    store_topics(db, [dataclasses.replace(CC, views=480_000)], "GTA 6", NOW)
    (topic,) = ranked_topics(db)
    assert topic["views"] == 480_000 and topic["views_per_hour"] == 2000


def test_angle_suggestions_follow_title_wording():
    angles = ["detail-spotting", "gta-v-comparison", "mechanic-explained", "what-you-missed"]
    assert suggest_angle("GTA 6 vs GTA V physics", angles) == "gta-v-comparison"
    assert suggest_angle("10 hidden details in the trailer", angles) == "detail-spotting"
    assert suggest_angle("How the wanted system works", angles) == "mechanic-explained"
    assert suggest_angle("Trailer 3 reaction", angles) == "what-you-missed"
    assert suggest_angle("GTA 6 vs GTA V", ["detail-spotting"]) == "detail-spotting"


def test_discover_logs_quota_per_run_and_stores_topics(db, settings):
    api = FakeApi()
    result = discover(db, settings, lambda on_call: client(api, on_call))
    keywords = len(settings.sources.discovery.keywords)
    assert result["quota"] == {"search": 2 * keywords, "general": keywords}  # trending + CC search per keyword
    assert result["topics"] == 2 * keywords and len(ranked_topics(db)) == 2
    assert [t["video_id"] for t in ranked_topics(db, creative_commons_only=True)] == [CC.id]
    rows = db.all("SELECT method, bucket, units FROM quota_log WHERE run_id = ?", result["run_id"])
    assert len(rows) == 3 * keywords and {r["method"] for r in rows} == {"search.list", "videos.list"}
    licences = [dict(r.url.params).get("videoLicense") for r in api.requests if r.url.path.endswith("/search")]
    assert licences == ["any", "creativeCommon"] * keywords
    assert not settings.library.exists()  # discovery never downloads


def test_failed_requests_still_count_against_quota(db, settings):
    with pytest.raises(ProviderError):
        discover(db, settings, lambda on_call: client(lambda r: httpx.Response(500, text="boom"), on_call))
    assert db.one("SELECT COUNT(*) FROM quota_log")[0] == 1


# -- source policy for URLs ---------------------------------------------------

def lookup(video_id):
    return {CC.id: CC, STD.id: STD}.get(video_id)


def test_creative_commons_upload_is_allowed(settings):
    decision, video = check_url(CC.url, settings, lookup)
    assert decision.allowed and decision.rule == RULE_CREATIVE_COMMONS and video == CC


def test_standard_licence_from_unlisted_channel_is_denied(settings):
    decision, _ = check_url(STD.url, settings, lookup)
    assert not decision.allowed and decision.rule is None


def test_allowlisted_channel_is_allowed_whatever_the_licence(root):
    sources = root / "config" / "sources.yaml"
    sources.write_text(sources.read_text().replace("allowlist_channels: []", "allowlist_channels: [UC_std]"))
    decision, _ = check_url(STD.url, load_settings(root), lookup)
    assert decision.allowed and decision.rule == RULE_ALLOWLISTED_CHANNEL


def test_creative_commons_rule_can_be_switched_off(root):
    sources = root / "config" / "sources.yaml"
    sources.write_text(sources.read_text().replace("allow_creative_commons: true", "allow_creative_commons: false"))
    assert not check_url(CC.url, load_settings(root), lookup)[0].allowed


@pytest.mark.parametrize("url", ["https://vimeo.com/1", "https://www.youtube.com/playlist?list=PL1", "https://youtu.be/zzzzzzzzzzz"])
def test_unknown_or_non_video_urls_are_denied(settings, url):
    assert not check_url(url, settings, lookup)[0].allowed


class FakeDownloader:
    def __init__(self, clip):
        self.clip, self.calls = clip, []

    def download(self, video, dest):
        self.calls.append(video.id)
        shutil.copy(self.clip, dest)


def test_denied_url_is_refused_and_nothing_is_written(db, settings, fixture_clip):
    downloader = FakeDownloader(fixture_clip)
    with pytest.raises(PolicyDenied):
        ingest_url(db, settings, STD.url, lookup, downloader)
    assert downloader.calls == []
    assert not settings.library.exists()
    assert db.one("SELECT COUNT(*) FROM sources")[0] == 0


def test_allowed_url_is_downloaded_and_recorded_with_its_rule(db, settings, fixture_clip):
    downloader = FakeDownloader(fixture_clip)
    source_id = ingest_url(db, settings, "https://youtu.be/aaaaaaaaaaa", lookup, downloader)
    row = db.one("SELECT * FROM sources WHERE id = ?", source_id)
    assert row["kind"] == "url" and row["url"] == CC.url and row["policy_rule"] == RULE_CREATIVE_COMMONS
    assert row["licence"] == "creativeCommon" and "UC_cc" in row["channel"] and row["ingested_at"] > 0
    assert row["path"] == str(settings.library / "aaaaaaaaaaa.mp4")
    assert ingest_url(db, settings, CC.url, lookup, downloader) == source_id and downloader.calls == [CC.id]


def test_video_from_ingested_url_carries_attribution(db, settings, make_pipeline, fixture_clip):
    ingest_url(db, settings, CC.url, lookup, FakeDownloader(fixture_clip))
    job_id = make_pipeline().make(settings.library / "aaaaaaaaaaa.mp4", "detail-spotting", live=False)
    sidecar = json.loads(open(db.one("SELECT sidecar_path FROM renders WHERE job_id = ?", job_id)["sidecar_path"]).read())
    credit = "Footage: CC Channel (UC_cc) - https://www.youtube.com/watch?v=aaaaaaaaaaa (Creative Commons Attribution)"
    assert sidecar["source_attribution"]["credit"] == credit and sidecar["description"].endswith(credit)
    assert sidecar["source_attribution"]["policy_rule"] == RULE_CREATIVE_COMMONS
    assert sidecar["source_attribution"]["licence"] == "creativeCommon"


def test_library_file_without_an_ingest_record_is_denied(db, settings, make_pipeline, fixture_clip):
    settings.library.mkdir()
    planted = settings.library / "planted.mp4"
    shutil.copy(fixture_clip, planted)
    with pytest.raises(PolicyDenied, match="not ingested through the source policy"):
        make_pipeline().make(planted, "detail-spotting", live=False)
    assert db.one("SELECT COUNT(*) FROM jobs")[0] == 0


def test_library_file_changed_after_ingest_is_denied(db, settings, make_pipeline, fixture_clip):
    ingest_url(db, settings, CC.url, lookup, FakeDownloader(fixture_clip))
    path = settings.library / "aaaaaaaaaaa.mp4"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(PolicyDenied, match="changed since it was ingested"):
        make_pipeline().make(path, "detail-spotting", live=False)
