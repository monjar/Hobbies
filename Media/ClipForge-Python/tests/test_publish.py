"""Publishing: approved renders only, dry run sends nothing, real publish stores the post ID and URL."""

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import httpx
import pytest

from clipforge.config import InstagramPublishCfg, YouTubePublishCfg
from clipforge.errors import ClipforgeError, ConfigError, ProviderError
from clipforge.publish.base import load_post, publish_job
from clipforge.publish.export import ExportPublisher
from clipforge.publish.google_auth import SCOPES, GoogleAuth, authorization_url, pkce_pair
from clipforge.publish.instagram import InstagramPublisher
from clipforge.publish.youtube import YouTubePublisher
from helpers import seed


class Recorder:
    """httpx mock transport that records requests and answers from a handler."""

    def __init__(self, handler):
        self.requests, self._handler = [], handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    @property
    def transport(self):
        return httpx.MockTransport(self)


def youtube_api(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        return httpx.Response(200, headers={"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=S1"})
    return httpx.Response(201, json={"id": "vid12345678", "status": {"privacyStatus": "public"}})


def youtube(api, **cfg):
    return YouTubePublisher(YouTubePublishCfg(**cfg), lambda: "TOKEN", transport=api.transport)


# -- gate ---------------------------------------------------------------------

@pytest.mark.parametrize("state", ["rendered", "pending_review"])
def test_unapproved_render_cannot_be_published_or_even_dry_run(db, settings, state):
    job_id = seed(db, settings, state=state)
    api = Recorder(youtube_api)
    for dry_run in (False, True):
        with pytest.raises(ClipforgeError, match="only approved renders"):
            publish_job(db, job_id, youtube(api), dry_run=dry_run)
    assert api.requests == [] and db.one("SELECT COUNT(*) FROM posts")[0] == 0
    assert db.job(job_id)["state"] == state


def test_rejected_render_cannot_be_published(db, settings):
    from clipforge.review import actions

    job_id = seed(db, settings)
    actions.reject(db, job_id, "no")
    with pytest.raises(ClipforgeError, match="only approved renders"):
        publish_job(db, job_id, ExportPublisher("tiktok", settings.out, True))


# -- youtube ------------------------------------------------------------------

def test_dry_run_returns_the_exact_request_and_sends_nothing(db, settings):
    job_id = seed(db, settings, state="approved")
    api = Recorder(youtube_api)
    requests = publish_job(db, job_id, youtube(api), dry_run=True)
    assert api.requests == []
    assert db.one("SELECT COUNT(*) FROM posts")[0] == 0 and db.job(job_id)["state"] == "approved"
    start = requests[0]
    assert start["method"] == "POST" and start["url"] == "https://www.googleapis.com/upload/youtube/v3/videos"
    assert start["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert start["json"]["snippet"] == {
        "title": "Red car", "description": "A detail you missed.\n\n#gta6 #gaming", "tags": ["gta6", "gaming"], "categoryId": "20",
    }
    assert start["json"]["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    assert "TOKEN" not in json.dumps(requests)


def test_real_publish_matches_the_dry_run_and_stores_post_id_and_url(db, settings):
    job_id = seed(db, settings, state="approved")
    api = Recorder(youtube_api)
    described = youtube(api).describe(load_post(db, job_id))
    result = publish_job(db, job_id, youtube(api))

    start, transfer = api.requests
    assert str(start.url) == "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet%2Cstatus"
    assert json.loads(start.content) == described[0]["json"]
    assert start.headers["authorization"] == "Bearer TOKEN"
    assert start.headers["x-upload-content-length"] == "10" and start.headers["x-upload-content-type"] == "video/mp4"
    assert transfer.method == "PUT" and str(transfer.url).endswith("upload_id=S1")
    assert transfer.content == b"0123456789" and transfer.headers["content-type"] == "video/mp4"

    assert (result.status, result.post_id, result.url) == ("published", "vid12345678", "https://www.youtube.com/watch?v=vid12345678")
    post = db.one("SELECT * FROM posts WHERE job_id = ?", job_id)
    assert (post["platform"], post["post_id"], post["url"]) == ("youtube", "vid12345678", result.url)
    assert db.job(job_id)["state"] == "published"


def test_publishing_twice_to_one_platform_uploads_once(db, settings):
    job_id = seed(db, settings, state="approved")
    api = Recorder(youtube_api)
    publish_job(db, job_id, youtube(api))
    again = publish_job(db, job_id, youtube(api))
    assert len(api.requests) == 2 and again.post_id == "vid12345678" and again.detail == {"already_done": True}
    assert db.one("SELECT COUNT(*) FROM posts")[0] == 1


def test_youtube_settings_reach_the_request(db, settings):
    job_id = seed(db, settings, state="approved")
    publisher = youtube(Recorder(youtube_api), privacy="unlisted", contains_synthetic_media=True, category_id="24")
    resource = publisher.describe(load_post(db, job_id))[0]["json"]
    assert resource["status"]["privacyStatus"] == "unlisted" and resource["status"]["containsSyntheticMedia"] is True
    assert resource["snippet"]["categoryId"] == "24"


def test_failed_upload_stores_nothing_and_leaves_the_job_approved(db, settings):
    job_id = seed(db, settings, state="approved")
    api = Recorder(lambda r: httpx.Response(403, json={"error": {"message": "quotaExceeded"}}))
    with pytest.raises(ProviderError, match="quotaExceeded"):
        publish_job(db, job_id, youtube(api))
    assert db.one("SELECT COUNT(*) FROM posts")[0] == 0 and db.job(job_id)["state"] == "approved"


# -- export -------------------------------------------------------------------

def test_export_writes_video_and_caption_and_keeps_the_job_approved(db, settings):
    job_id = seed(db, settings, state="approved")
    result = publish_job(db, job_id, ExportPublisher("tiktok", settings.out, ai_disclosure=True))
    video, caption = Path(result.detail["video"]), Path(result.detail["caption"])
    assert result.status == "exported" and video.read_bytes() == b"0123456789"
    assert video.parent.name == "tiktok" and video.parent.parent.parent.name == "export"
    text = caption.read_text()
    assert "Red car" in text and "#gta6 #gaming" in text and "AI-generated content label" in text
    assert db.job(job_id)["state"] == "approved"  # nothing is public yet
    assert db.one("SELECT status FROM posts WHERE job_id = ?", job_id)["status"] == "exported"


def test_export_dry_run_writes_no_files(db, settings):
    job_id = seed(db, settings, state="approved")
    plan = publish_job(db, job_id, ExportPublisher("tiktok", settings.out, ai_disclosure=False), dry_run=True)
    assert [step["action"] for step in plan] == ["copy", "write"] and "AI-generated" not in plan[1]["content"]
    assert not (settings.out / "export").exists()


def test_job_can_go_to_several_platforms(db, settings):
    job_id = seed(db, settings, state="approved")
    publish_job(db, job_id, ExportPublisher("tiktok", settings.out, True))
    publish_job(db, job_id, youtube(Recorder(youtube_api)))
    publish_job(db, job_id, ExportPublisher("instagram", settings.out, True))  # still allowed once published
    assert {r["platform"]: r["status"] for r in db.all("SELECT platform, status FROM posts")} == {
        "tiktok": "exported", "youtube": "published", "instagram": "exported",
    }
    assert db.job(job_id)["state"] == "published"


# -- instagram ----------------------------------------------------------------

def instagram_api(statuses):
    queue = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "rupload.facebook.com":
            return httpx.Response(200, json={"success": True})
        if path.endswith("/IGUSER/media"):
            return httpx.Response(200, json={"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/C1"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "M9"})
        if path.endswith("/C1"):
            return httpx.Response(200, json={"status_code": queue.pop(0)})
        return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})

    return Recorder(handler)


def instagram(api, **cfg):
    return InstagramPublisher(InstagramPublishCfg(mode="api", **cfg), "IGTOKEN", "IGUSER", transport=api.transport, sleep=lambda s: None)


def test_instagram_reel_flow_container_upload_wait_publish(db, settings):
    job_id = seed(db, settings, state="approved")
    api = instagram_api(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    result = publish_job(db, job_id, instagram(api))
    create, upload, *polls, publish, link = api.requests
    assert str(create.url) == "https://graph.facebook.com/v27.0/IGUSER/media"
    assert json.loads(create.content) == {
        "media_type": "REELS", "upload_type": "resumable", "caption": "A detail you missed.\n\n#gta6 #gaming", "is_ai_generated": True,
    }
    assert str(upload.url) == "https://rupload.facebook.com/ig-api-upload/v27.0/C1"
    assert (upload.headers["authorization"], upload.headers["offset"], upload.headers["file_size"]) == ("OAuth IGTOKEN", "0", "10")
    assert upload.content == b"0123456789" and len(polls) == 3
    assert json.loads(publish.content) == {"creation_id": "C1"}
    assert (result.post_id, result.url) == ("M9", "https://www.instagram.com/reel/abc/")
    assert db.job(job_id)["state"] == "published"


def test_instagram_processing_error_is_not_published(db, settings):
    job_id = seed(db, settings, state="approved")
    api = instagram_api(["IN_PROGRESS", "ERROR"])
    with pytest.raises(ProviderError, match="could not process"):
        publish_job(db, job_id, instagram(api))
    assert not any(r.url.path.endswith("/media_publish") for r in api.requests)
    assert db.one("SELECT COUNT(*) FROM posts")[0] == 0 and db.job(job_id)["state"] == "approved"


def test_instagram_ai_disclosure_can_be_switched_off_and_dry_run_hides_the_token(db, settings):
    job_id = seed(db, settings, state="approved")
    api = instagram_api([])
    plan = publish_job(db, job_id, instagram(api, ai_disclosure=False), dry_run=True)
    assert "is_ai_generated" not in plan[0]["json"] and "IGTOKEN" not in json.dumps(plan) and api.requests == []


# -- google oauth -------------------------------------------------------------

def test_authorization_url_requests_offline_access_with_pkce():
    verifier, challenge = pkce_pair()
    url = urllib.parse.urlsplit(authorization_url("CID", "http://127.0.0.1:9004", challenge, "STATE"))
    params = dict(urllib.parse.parse_qsl(url.query))
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert params["scope"].split() == list(SCOPES) and params["response_type"] == "code"
    assert params["code_challenge"] == challenge != verifier and params["code_challenge_method"] == "S256"
    assert params["access_type"] == "offline" and params["redirect_uri"] == "http://127.0.0.1:9004"


def token_endpoint(request: httpx.Request) -> httpx.Response:
    form = dict(urllib.parse.parse_qsl(request.content.decode()))
    if form["grant_type"] == "authorization_code":
        return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    if form.get("refresh_token") == "R1":
        return httpx.Response(200, json={"access_token": "A2", "expires_in": 3600})
    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been revoked."})


def test_login_exchanges_the_code_and_later_runs_refresh(tmp_path):
    api = Recorder(token_endpoint)
    token_path = tmp_path / "state" / "google_token.json"
    auth = GoogleAuth("CID", "SECRET", token_path, transport=api.transport)

    def browser(url: str) -> None:  # plays the user approving in the browser
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        callback = f"{params['redirect_uri']}/?state={params['state']}&code=CODE1"
        threading.Thread(target=lambda: urllib.request.urlopen(callback).read(), daemon=True).start()

    auth.login(browser, timeout_s=10)
    exchange = dict(urllib.parse.parse_qsl(api.requests[0].content.decode()))
    assert exchange["code"] == "CODE1" and exchange["grant_type"] == "authorization_code"
    assert exchange["code_verifier"] and exchange["redirect_uri"].startswith("http://127.0.0.1:")
    assert json.loads(token_path.read_text()) == {"refresh_token": "R1"}
    assert oct(token_path.stat().st_mode & 0o777) == "0o600"
    assert auth.access_token() == "A1" and len(api.requests) == 1  # still fresh, no second call

    later = GoogleAuth("CID", "SECRET", token_path, transport=api.transport)
    assert later.access_token() == "A2"
    assert dict(urllib.parse.parse_qsl(api.requests[1].content.decode()))["grant_type"] == "refresh_token"


def test_login_rejects_a_callback_with_the_wrong_state(tmp_path):
    auth = GoogleAuth("CID", "SECRET", tmp_path / "t.json", transport=Recorder(token_endpoint).transport)

    def forged(url: str) -> None:
        redirect = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["redirect_uri"]
        threading.Thread(target=lambda: urllib.request.urlopen(f"{redirect}/?state=WRONG&code=X").read(), daemon=True).start()

    with pytest.raises(ClipforgeError, match="did not match"):
        auth.login(forged, timeout_s=10)
    assert not (tmp_path / "t.json").exists()


def test_missing_or_revoked_token_gives_a_clear_error(tmp_path):
    auth = GoogleAuth("CID", "SECRET", tmp_path / "t.json", transport=Recorder(token_endpoint).transport)
    with pytest.raises(ConfigError, match="clipforge auth youtube"):
        auth.access_token()
    (tmp_path / "t.json").write_text('{"refresh_token": "OLD"}')
    with pytest.raises(ClipforgeError, match="revoked"):
        auth.access_token()
