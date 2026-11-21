"""YouTube upload through videos.insert with the resumable protocol. Per docs/integrations.md."""

import json
from collections.abc import Callable

import httpx

from ..config import YouTubePublishCfg
from ..errors import ProviderError
from .base import Post, PublishResult

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
TITLE_LIMIT = 100
REDACTED = "Bearer <access token>"


class YouTubePublisher:
    platform = "youtube"

    def __init__(self, cfg: YouTubePublishCfg, access_token: Callable[[], str], transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._access_token = access_token
        self._http = httpx.Client(timeout=httpx.Timeout(30.0, write=600.0, read=600.0), transport=transport)

    def _resource(self, post: Post) -> dict:
        return {
            "snippet": {
                "title": post.title[:TITLE_LIMIT],
                "description": post.caption,
                "tags": [tag.lstrip("#") for tag in post.hashtags],
                "categoryId": self.cfg.category_id,
            },
            "status": {
                "privacyStatus": self.cfg.privacy,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": self.cfg.contains_synthetic_media,
            },
        }

    def _init_request(self, post: Post, authorization: str) -> dict:
        return {
            "method": "POST",
            "url": UPLOAD_URL,
            "params": {"uploadType": "resumable", "part": "snippet,status"},
            "headers": {
                "Authorization": authorization,
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(post.video.stat().st_size),
                "X-Upload-Content-Type": "video/mp4",
            },
            "json": self._resource(post),
        }

    def describe(self, post: Post) -> list[dict]:
        return [
            self._init_request(post, REDACTED),
            {
                "method": "PUT",
                "url": "<Location header of the first response>",
                "headers": {"Authorization": REDACTED, "Content-Type": "video/mp4",
                            "Content-Length": str(post.video.stat().st_size)},
                "body": f"<bytes of {post.video}>",
            },
        ]

    def _check(self, response: httpx.Response, step: str, expected: int) -> None:
        if response.status_code != expected:
            try:
                message = response.json()["error"]["message"]
            except Exception:
                message = response.text[:200]
            raise ProviderError(f"youtube upload {step} failed ({response.status_code}): {message}")

    def publish(self, post: Post) -> PublishResult:
        authorization = f"Bearer {self._access_token()}"
        request = self._init_request(post, authorization)
        try:
            start = self._http.post(
                request["url"], params=request["params"], headers=request["headers"],
                content=json.dumps(request["json"]).encode(),
            )
            self._check(start, "start", 200)
            session_url = start.headers.get("Location")
            if not session_url:
                raise ProviderError("youtube upload start returned no session URL")
            with post.video.open("rb") as handle:
                done = self._http.put(
                    session_url,
                    headers={"Authorization": authorization, "Content-Type": "video/mp4",
                             "Content-Length": str(post.video.stat().st_size)},
                    content=handle.read(),  # renders are 20-40 s, a few MB at most
                )
            self._check(done, "transfer", 201)
        except httpx.HTTPError as exc:
            raise ProviderError(f"youtube upload connection error: {type(exc).__name__}: {exc}") from exc
        video = done.json()
        return PublishResult(
            "published", video["id"], f"https://www.youtube.com/watch?v={video['id']}",
            {"privacyStatus": video.get("status", {}).get("privacyStatus")},
        )
